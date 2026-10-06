// TypeORM entity — @Entity + @Column + @ManyToOne/@JoinColumn FK (point 2).
import { Entity, Column, PrimaryGeneratedColumn, ManyToOne, JoinColumn } from "typeorm";
import { Team } from "./team.entity";

@Entity("accounts")
export class Account {
  @PrimaryGeneratedColumn()
  id: number;

  @Column({ type: "varchar", nullable: false })
  username: string;

  @Column({ nullable: true })
  bio: string;

  @ManyToOne(() => Team)
  @JoinColumn({ name: "team_id" })
  team: Team;
}
